/**
 * Handlers for proposal and bundle ontology studio subcommands:
 * proposal list, proposal show, proposal accept, proposal reject, proposal rebase, proposal refine,
 * export, promote, bundle export, bundle import, bundle clone, starter-pack list, starter-pack apply,
 * text add, staged list, staged promote.
 */

import { createHash } from "node:crypto";
import { CliDomainError } from "../error-handler";
import type { RemoteHttpClient } from "./http-client";
import { MIN_RATIONALE_LENGTH, normalizeStringArrayOption } from "./types";

export async function handleStudioProposalsAndBundles(
	client: RemoteHttpClient,
	studioSub: string,
	options: Record<string, unknown>,
): Promise<{ handled: boolean; payload?: unknown }> {
	switch (studioSub) {
		case "proposal list": {
			const sid = options.session;
			if (!sid) {
				throw new CliDomainError(
					"SYNTAX_INVALID_ARGUMENT",
					"Missing required --session flag",
					400,
				);
			}
			const payload = await client.getJson(
				`/api/v1/ontology/studio/sessions/${encodeURIComponent(String(sid))}/proposals`,
			);
			return { handled: true, payload };
		}

		case "proposal show": {
			const sid = options.session;
			const pid = options.proposal_id;
			if (!sid || !pid) {
				throw new CliDomainError(
					"SYNTAX_INVALID_ARGUMENT",
					"Missing required --session or --proposal-id flag",
					400,
				);
			}
			const payload = await client.getJson(
				`/api/v1/ontology/studio/sessions/${encodeURIComponent(String(sid))}/proposals/${encodeURIComponent(String(pid))}`,
			);
			return { handled: true, payload };
		}

		case "proposal accept": {
			const sid = options.session;
			const pid = options.proposal_id;
			if (!sid || !pid) {
				throw new CliDomainError(
					"SYNTAX_INVALID_ARGUMENT",
					"Missing required --session or --proposal-id flag",
					400,
				);
			}

			const extraHeaders: Record<string, string> = {};
			const cleanAcceptSha =
				options.base_sha256 !== undefined && options.base_sha256 !== null
					? String(options.base_sha256).trim().replace(/^"|"$/g, "")
					: undefined;
			if (cleanAcceptSha !== undefined) {
				extraHeaders["If-Match"] = `"${cleanAcceptSha}"`;
			}

			const waiverInfo = await client.resolveWaiverHeaders(options);
			if (waiverInfo.low_confidence_waiver) {
				Object.assign(extraHeaders, waiverInfo.headers);
			}

			const body: Record<string, unknown> = {
				base_sha256: cleanAcceptSha,
				allow_self_accepted_proposals: Boolean(
					options.allow_self_accepted_proposals,
				),
				low_confidence_waiver: waiverInfo.low_confidence_waiver,
				grounding_rationale: waiverInfo.grounding_rationale,
				self_acceptance_override: Boolean(
					options.allow_self_accepted_proposals,
				),
			};
			if (options.ungrounded_concept) {
				body.ungrounded_concept = normalizeStringArrayOption(
					options.ungrounded_concept,
				);
			}
			if (options.evidence_grounding_file) {
				body.evidence_grounding_file = options.evidence_grounding_file;
			}

			const payload = await client.postJson(
				`/api/v1/ontology/studio/sessions/${encodeURIComponent(String(sid))}/proposals/${encodeURIComponent(String(pid))}/accept`,
				body,
				extraHeaders,
			);
			return { handled: true, payload };
		}

		case "proposal reject": {
			const sid = options.session;
			const pid = options.proposal_id;
			if (!sid || !pid) {
				throw new CliDomainError(
					"SYNTAX_INVALID_ARGUMENT",
					"Missing required --session or --proposal-id flag",
					400,
				);
			}
			const body: Record<string, unknown> = {};
			if (options.reason) {
				body.reason = options.reason;
			}
			const payload = await client.postJson(
				`/api/v1/ontology/studio/sessions/${encodeURIComponent(String(sid))}/proposals/${encodeURIComponent(String(pid))}/reject`,
				body,
			);
			return { handled: true, payload };
		}

		case "proposal rebase": {
			const sid = options.session;
			const pid = options.proposal_id;
			if (!sid || !pid) {
				throw new CliDomainError(
					"SYNTAX_INVALID_ARGUMENT",
					"Missing required --session or --proposal-id flag",
					400,
				);
			}
			const rawRebaseSha =
				options.base_sha256 !== undefined && options.base_sha256 !== null
					? String(options.base_sha256)
					: options.head_sha256 !== undefined && options.head_sha256 !== null
						? String(options.head_sha256)
						: undefined;
			let targetSha =
				rawRebaseSha !== undefined
					? rawRebaseSha.trim().replace(/^"|"$/g, "")
					: undefined;
			if (!targetSha) {
				const sessionDetail = await client.fetchSessionDetail(String(sid));
				targetSha = String(sessionDetail.head_sha256 ?? "")
					.trim()
					.replace(/^"|"$/g, "");
			}
			const extraHeaders: Record<string, string> = {};
			if (targetSha) {
				extraHeaders["If-Match"] = `"${targetSha}"`;
			}
			const body: Record<string, unknown> = {
				base_sha256: targetSha,
			};
			const payload = await client.postJson(
				`/api/v1/ontology/studio/sessions/${encodeURIComponent(String(sid))}/proposals/${encodeURIComponent(String(pid))}/rebase`,
				body,
				extraHeaders,
			);
			return { handled: true, payload };
		}

		case "proposal refine": {
			const sid = options.session;
			const pid = options.proposal_id || options.proposal;
			if (!sid) {
				throw new CliDomainError(
					"SYNTAX_INVALID_ARGUMENT",
					"Missing required --session flag",
					400,
				);
			}
			if (!pid) {
				throw new CliDomainError(
					"SYNTAX_INVALID_ARGUMENT",
					"Missing required --proposal-id flag",
					400,
				);
			}
			const steering = options.steering_prompt;
			if (!steering) {
				throw new CliDomainError(
					"SYNTAX_INVALID_ARGUMENT",
					"Missing required --steering-prompt flag",
					400,
				);
			}
			const body: Record<string, unknown> = {
				steering_prompt: steering,
				mode: options.mode || "deterministic",
				stream: Boolean(options.stream),
			};
			const payload = await client.postJson(
				`/api/v1/ontology/studio/sessions/${encodeURIComponent(String(sid))}/proposals/${encodeURIComponent(String(pid))}/refine`,
				body,
			);
			return { handled: true, payload };
		}

		case "export": {
			const sid = options.session;
			if (!sid) {
				throw new CliDomainError(
					"SYNTAX_INVALID_ARGUMENT",
					"Missing required --session flag",
					400,
				);
			}
			const fmt = String(options.export_format || "yaml");
			const ver =
				options.version && typeof options.version === "string"
					? `&version=${encodeURIComponent(options.version)}`
					: "";
			const headers = {
				...client.getHeaders(undefined, { isStudioRequest: true }),
				Accept: "*/*",
			};
			const res = await client.fetchImpl(
				`${client.baseUrl}/api/v1/ontology/studio/sessions/${encodeURIComponent(String(sid))}/export?export_format=${encodeURIComponent(fmt)}${ver}`,
				{ headers },
			);
			if (!res.ok) {
				throw await client.parseErrorResponse(res);
			}
			const rawText = await res.text();
			const sha256 = createHash("sha256")
				.update(rawText, "utf-8")
				.digest("hex");

			if (options.out && typeof options.out === "string") {
				const fs = await import("node:fs/promises");
				await fs.writeFile(options.out, rawText, "utf-8");
			}

			const wantsJson = options.json === true || options.format === "json";
			if (wantsJson) {
				return {
					handled: true,
					payload: {
						session_id: String(sid),
						format: fmt,
						export_format: fmt,
						...(options.version && typeof options.version === "string"
							? { version: options.version }
							: {}),
						...(options.out && typeof options.out === "string"
							? { out_path: options.out }
							: {}),
						sha256,
						content: rawText,
					},
				};
			}
			return { handled: true, payload: rawText };
		}

		case "promote": {
			const sid = options.session;
			if (!sid) {
				throw new CliDomainError(
					"SYNTAX_INVALID_ARGUMENT",
					"Missing required --session flag",
					400,
				);
			}
			client.assertLoopbackForServerPath("--workspace", options.workspace);
			const rationale = options.rationale;
			if (
				!rationale ||
				String(rationale).trim().length < MIN_RATIONALE_LENGTH
			) {
				throw new CliDomainError(
					"PROMOTION_RATIONALE_REQUIRED",
					`Promotion requires rationale (min ${MIN_RATIONALE_LENGTH} chars)`,
					400,
				);
			}
			const headSha = String(options.head_sha256 ?? "")
				.trim()
				.replace(/^"|"$/g, "");
			const wsSha = String(options.workspace_sha256 ?? "")
				.trim()
				.replace(/^"|"$/g, "");
			const extraHeaders: Record<string, string> = {
				"If-Match": `"${headSha}"`,
				"X-OpenDV-Workspace-If-Match": `"${wsSha}"`,
			};
			const body: Record<string, unknown> = {
				workspace_sha256: wsSha,
				head_sha256: headSha,
				rationale: options.rationale,
				workspace: options.workspace,
				acknowledge_historical_contract_warning: Boolean(
					options.acknowledge_historical_contract_warning,
				),
				historical_contract_rationale: options.historical_contract_rationale,
			};
			const payload = await client.postJson(
				`/api/v1/ontology/studio/sessions/${encodeURIComponent(String(sid))}/promote`,
				body,
				extraHeaders,
			);
			return { handled: true, payload };
		}

		case "bundle export": {
			const sid = options.session;
			if (!sid) {
				throw new CliDomainError(
					"SYNTAX_INVALID_ARGUMENT",
					"Missing required --session flag",
					400,
				);
			}
			const body: Record<string, unknown> = {
				acknowledge_confidential_source_export: Boolean(
					options.acknowledge_confidential_source_export,
				),
				export_rationale: options.export_rationale,
			};
			const res = await client.fetchImpl(
				`${client.baseUrl}/api/v1/ontology/studio/sessions/${encodeURIComponent(String(sid))}/bundle`,
				{
					method: "POST",
					headers: client.getHeaders(undefined, { isStudioRequest: true }),
					body: JSON.stringify(body),
				},
			);
			if (!res.ok) {
				throw await client.parseErrorResponse(res);
			}
			const contentType = res.headers?.get("content-type") || "";
			if (contentType.includes("json")) {
				return { handled: true, payload: await res.json() };
			}
			const arrayBuffer = await res.arrayBuffer();
			const buffer = Buffer.from(arrayBuffer);
			const outPath =
				(options.out as string) ||
				(options.out_path as string) ||
				`${sid}.odvstudio.zip`;
			const fs = await import("node:fs/promises");
			await fs.writeFile(outPath, buffer);
			return {
				handled: true,
				payload: {
					session_id: sid,
					bundle_path: outPath,
					size_bytes: buffer.byteLength,
				},
			};
		}

		case "bundle import": {
			const filePath = (options.file_path || options.file) as string;
			if (!filePath) {
				throw new CliDomainError(
					"SYNTAX_INVALID_ARGUMENT",
					"Missing required --file flag",
					400,
				);
			}
			let fileData: Uint8Array;
			try {
				const fs = await import("node:fs/promises");
				fileData = await fs.readFile(filePath);
			} catch (err: unknown) {
				if (
					(err instanceof Error &&
						"code" in err &&
						(err as { code: string }).code === "ENOENT") ||
					String(err).includes("ENOENT")
				) {
					throw new CliDomainError(
						"FILE_NOT_FOUND",
						`Bundle file not found: ${filePath}`,
						404,
					);
				}
				throw new CliDomainError(
					"SYNTAX_INVALID_ARGUMENT",
					`Failed to read bundle file: ${err}`,
					400,
				);
			}
			const fileName = filePath.split(/[\\/]/).pop() || "bundle.zip";
			const formData = new FormData();
			formData.append("file", new Blob([new Uint8Array(fileData)]), fileName);

			const queryParams = new URLSearchParams();
			if (options.new_session_id) {
				queryParams.set("new_session_id", String(options.new_session_id));
				formData.append("new_session_id", String(options.new_session_id));
			}
			if (options.force) {
				queryParams.set("force", "true");
				formData.append("force", "true");
			}
			if (options.acknowledge_unverified_import_attribution) {
				queryParams.set("acknowledge_unverified_import_attribution", "true");
			}
			if (options.acknowledgement_rationale) {
				queryParams.set(
					"acknowledgement_rationale",
					String(options.acknowledgement_rationale),
				);
			}
			if (options.acknowledge_historical_contract_warning) {
				queryParams.set("acknowledge_historical_contract_warning", "true");
			}
			if (options.historical_contract_rationale) {
				queryParams.set(
					"historical_contract_rationale",
					String(options.historical_contract_rationale),
				);
			}
			const q = queryParams.toString() ? `?${queryParams.toString()}` : "";
			const payload = await client.postFormData(
				`/api/v1/ontology/studio/sessions/bundle/import${q}`,
				formData,
			);
			return { handled: true, payload };
		}

		case "bundle clone": {
			const sid = options.session;
			if (!sid) {
				throw new CliDomainError(
					"SYNTAX_INVALID_ARGUMENT",
					"Missing required --session flag",
					400,
				);
			}
			const body: Record<string, unknown> = {
				new_session_id: options.new_session_id,
				session_label: options.session_label,
			};
			const payload = await client.postJson(
				`/api/v1/ontology/studio/sessions/${encodeURIComponent(String(sid))}/bundle/clone`,
				body,
			);
			return { handled: true, payload };
		}

		case "starter-pack list": {
			const payload = await client.getJson("/api/v1/ontology/studio/starter-packs");
			return { handled: true, payload };
		}

		case "starter-pack apply": {
			const sid = options.session;
			if (!sid) {
				throw new CliDomainError(
					"SYNTAX_INVALID_ARGUMENT",
					"Missing required --session flag",
					400,
				);
			}
			const pack = options.pack;
			if (!pack) {
				throw new CliDomainError(
					"SYNTAX_INVALID_ARGUMENT",
					"Missing required --pack flag",
					400,
				);
			}
			const body: Record<string, unknown> = {
				pack_id: pack,
				pack_ids: [pack],
			};
			const payload = await client.postJson(
				`/api/v1/ontology/studio/sessions/${encodeURIComponent(String(sid))}/starter-packs/apply`,
				body,
			);
			return { handled: true, payload };
		}

		case "text add": {
			const sid = options.session;
			if (!sid) {
				throw new CliDomainError(
					"SYNTAX_INVALID_ARGUMENT",
					"Missing required --session flag",
					400,
				);
			}
			let textContent = options.text as string | undefined;
			const filePath = (options.file || options.file_path) as
				| string
				| undefined;
			if (!textContent && !filePath) {
				throw new CliDomainError(
					"SYNTAX_INVALID_ARGUMENT",
					"Either --text or --file must be provided",
					400,
				);
			}
			if (filePath && !textContent) {
				try {
					const fs = await import("node:fs/promises");
					textContent = await fs.readFile(filePath, "utf-8");
				} catch (err: unknown) {
					throw new CliDomainError(
						"FILE_NOT_FOUND",
						`File not found: ${filePath}`,
						404,
					);
				}
			}
			const body: Record<string, unknown> = {
				text: textContent || "",
				source_name: options.source_name || "domain_notes.txt",
				do_not_send_to_agent: false,
			};
			const payload = await client.postJson(
				`/api/v1/ontology/studio/sessions/${encodeURIComponent(String(sid))}/sources/text`,
				body,
			);
			return { handled: true, payload };
		}

		case "staged list": {
			const sid = options.session;
			if (!sid) {
				throw new CliDomainError(
					"SYNTAX_INVALID_ARGUMENT",
					"Missing required --session flag",
					400,
				);
			}
			const payload = await client.getJson(
				`/api/v1/ontology/studio/sessions/${encodeURIComponent(String(sid))}/staged-candidates`,
			);
			return { handled: true, payload };
		}

		case "staged promote": {
			const sid = options.session;
			const candId = options.candidate_id || options.candidate;
			if (!sid) {
				throw new CliDomainError(
					"SYNTAX_INVALID_ARGUMENT",
					"Missing required --session flag",
					400,
				);
			}
			if (!candId) {
				throw new CliDomainError(
					"SYNTAX_INVALID_ARGUMENT",
					"Missing required --candidate-id flag",
					400,
				);
			}
			const synonyms =
				typeof options.synonyms === "string"
					? (options.synonyms as string).split(",").map((s) => s.trim())
					: (options.synonyms as string[] | undefined);
			const isWaiver = Boolean(options.waiver);
			const body: Record<string, unknown> = {
				path: isWaiver ? "path_b" : "path_a",
				synonyms: isWaiver ? null : synonyms || [],
				waiver_rationale: isWaiver ? options.rationale || null : null,
			};
			const payload = await client.postJson(
				`/api/v1/ontology/studio/sessions/${encodeURIComponent(String(sid))}/staged-candidates/${encodeURIComponent(String(candId))}/promote`,
				body,
			);
			return { handled: true, payload };
		}

		default:
			throw new CliDomainError(
				"INVALID_REQUEST",
				`Unknown ontology studio subcommand: ${studioSub}`,
				400,
			);
	}
}
