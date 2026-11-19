/**
 * Remote handlers for Phase 2 Ontology Studio Wizard subcommands:
 * `attach-source`, `fibo-qa`, and `logs` (incl. `--follow` SSE streaming).
 */

import { SseStreamParser } from "@opendv/protocol-ts";
import { CliDomainError } from "../error-handler";
import { isClientUploadableFile, stageCasBlob } from "./cas";
import type { RemoteHttpClient } from "./http-client";

export function mapWizardExitCode(
	httpStatus: number | undefined,
	errorCode: string,
): number | undefined {
	const codeUp = String(errorCode || "").toUpperCase();
	if (httpStatus === 422 || codeUp === "AGENT_NOT_CONFIGURED") {
		return 10;
	}
	if (httpStatus === 409 || codeUp === "SESSION_JOB_ACTIVE") {
		return 20;
	}
	if (codeUp.includes("PAUSED")) {
		return 30;
	}
	if (
		httpStatus === 400 ||
		codeUp === "INVALID_REQUEST" ||
		codeUp === "SYNTAX_INVALID_ARGUMENT" ||
		codeUp === "CREDENTIALS_REF_INVALID" ||
		codeUp === "PLAINTEXT_CREDENTIAL_FORBIDDEN" ||
		codeUp === "UNSAFE_SQL_QUERY"
	) {
		return 40;
	}
	return undefined;
}

export function withWizardExitCode(err: unknown): never {
	if (err instanceof CliDomainError) {
		const override = mapWizardExitCode(err.httpStatus, err.errorCode);
		if (override !== undefined) {
			throw new CliDomainError(
				err.errorCode,
				err.message,
				err.httpStatus,
				override,
			);
		}
		throw err;
	}
	throw err;
}

async function consumeLogsSse(
	client: RemoteHttpClient,
	streamUrl: string,
): Promise<{ entries: unknown[]; streamEnd: unknown }> {
	const res = await client.fetchImpl(streamUrl, {
		method: "GET",
		headers: {
			Accept: "text/event-stream",
			...client.getHeaders(),
		},
	});
	if (!res.ok) {
		const errText = await res.text().catch(() => "");
		let parsed: Record<string, unknown> | null = null;
		try {
			parsed = JSON.parse(errText) as Record<string, unknown>;
		} catch {
			parsed = null;
		}
		throw new CliDomainError(
			String(parsed?.error_code ?? "EXECUTION_FAILURE"),
			String(parsed?.detail ?? (errText || `HTTP ${res.status}`)),
			res.status,
		);
	}
	const parser = new SseStreamParser();
	const entries: unknown[] = [];
	let streamEnd: unknown = null;
	const reader = res.body?.getReader();
	if (!reader) {
		const raw = await res.text();
		for (const ev of parser.feed(raw)) {
			if (ev.event === "log_entry") {
				try {
					entries.push(JSON.parse(ev.data));
				} catch {
					entries.push({ raw: ev.data });
				}
			} else if (ev.event === "stream_end") {
				streamEnd = ev.data;
			}
		}
		return { entries, streamEnd };
	}
	const decoder = new TextDecoder();
	while (true) {
		const { done, value } = await reader.read();
		if (done) break;
		const events = parser.feed(decoder.decode(value, { stream: true }));
		for (const ev of events) {
			if (ev.event === "log_entry") {
				try {
					entries.push(JSON.parse(ev.data));
				} catch {
					entries.push({ raw: ev.data });
				}
			} else if (ev.event === "stream_end") {
				streamEnd = ev.data;
			}
		}
	}
	return { entries, streamEnd };
}

export async function handleStudioWizard(
	client: RemoteHttpClient,
	studioSub: string,
	enrichedOptions: Record<string, unknown>,
): Promise<{ handled: boolean; payload: unknown }> {
	const sid =
		(enrichedOptions.session as string | undefined) ||
		(enrichedOptions.session_id as string | undefined);

	if (studioSub === "attach-source") {
		if (!sid) {
			throw new CliDomainError(
				"SYNTAX_INVALID_ARGUMENT",
				"Missing required --session flag for attach-source",
				400,
			);
		}
		const adapter = enrichedOptions.adapter as string | undefined;
		const uri = enrichedOptions.uri as string | undefined;
		if (!adapter || !uri) {
			throw new CliDomainError(
				"SYNTAX_INVALID_ARGUMENT",
				"Missing required --adapter or --uri flag",
				400,
			);
		}
		const body: Record<string, unknown> = {
			adapter,
			uri,
		};
		if (enrichedOptions.table !== undefined) body.table = enrichedOptions.table;
		if (enrichedOptions.namespace !== undefined) {
			body.namespace = enrichedOptions.namespace;
		}
		if (enrichedOptions.credentials_ref !== undefined) {
			body.credentials_ref = enrichedOptions.credentials_ref;
		}
		if (enrichedOptions.description !== undefined) {
			body.description = enrichedOptions.description;
		}
		if (enrichedOptions.do_not_send_to_agent !== undefined) {
			body.do_not_send_to_agent = Boolean(enrichedOptions.do_not_send_to_agent);
		}
		if (
			isClientUploadableFile(uri) &&
			!/^https?:\/\//i.test(uri) &&
			!/^s3:\/\//i.test(uri)
		) {
			body.cas_hash = await stageCasBlob(client, uri);
		}
		const payload = await client.postJson(
			`/api/v1/ontology/studio/sessions/${encodeURIComponent(sid)}/sources/connectors`,
			body,
		);
		return { handled: true, payload };
	}

	if (studioSub === "fibo-qa") {
		if (!sid) {
			throw new CliDomainError(
				"SYNTAX_INVALID_ARGUMENT",
				"Missing required --session flag for fibo-qa",
				400,
			);
		}
		const question = enrichedOptions.question as string | undefined;
		if (!question) {
			throw new CliDomainError(
				"SYNTAX_INVALID_ARGUMENT",
				"Missing required --question flag for fibo-qa",
				400,
			);
		}
		const body: Record<string, unknown> = { question };
		if (enrichedOptions.source_id) {
			body.source_ids = [String(enrichedOptions.source_id)];
		}
		if (enrichedOptions.concept_uri) {
			body.concept_uri = String(enrichedOptions.concept_uri);
		}
		const payload = await client.postJson(
			`/api/v1/ontology/studio/sessions/${encodeURIComponent(sid)}/fibo-qa`,
			body,
		);
		return { handled: true, payload };
	}

	if (studioSub === "logs") {
		if (!sid) {
			throw new CliDomainError(
				"SYNTAX_INVALID_ARGUMENT",
				"Missing required --session or --session-id flag for logs",
				400,
			);
		}
		const params = new URLSearchParams();
		if (enrichedOptions.stage) {
			params.set("stage_id", String(enrichedOptions.stage));
		}
		if (enrichedOptions.step !== undefined) {
			params.set("wizard_step", String(enrichedOptions.step));
		}
		if (enrichedOptions.category) {
			params.set("category", String(enrichedOptions.category));
		}
		if (enrichedOptions.persona) {
			params.set("persona", String(enrichedOptions.persona));
		}
		const encSid = encodeURIComponent(sid);
		if (enrichedOptions.follow) {
			params.set("once", "true");
			const qs = params.toString() ? `?${params.toString()}` : "";
			const { entries } = await consumeLogsSse(
				client,
				`${client.baseUrl}/api/v1/ontology/studio/sessions/${encSid}/logs/stream${qs}`,
			);
			return {
				handled: true,
				payload: {
					session_id: sid,
					entries,
					total: entries.length,
					follow: true,
				},
			};
		}
		const qs = params.toString() ? `?${params.toString()}` : "";
		const payload = await client.getJson(
			`/api/v1/ontology/studio/sessions/${encSid}/logs${qs}`,
		);
		return { handled: true, payload };
	}

	return { handled: false, payload: null };
}
