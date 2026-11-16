/**
 * Content-Addressed Storage (CAS) client utilities for opendvctl.
 */

import { createHash } from "node:crypto";
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { CliDomainError } from "../error-handler";
import type { RemoteHttpClient } from "./http-client";

export function isClientUploadableFile(uriOrPath: string): boolean {
	const clean =
		uriOrPath
			.replace(/^sqlite:\/\/\/?/i, "")
			.replace(/^file:\/\/\/?/i, "")
			.replace(/^duckdb:\/\/\/?/i, "")
			.split(/[?#]/)[0] ?? "";
	const ext = path.extname(clean).toLowerCase();
	return [
		".sqlite",
		".sqlite3",
		".db",
		".duckdb",
		".ddb",
		".parquet",
		".pq",
		".csv",
		".tsv",
		".json",
		".jsonl",
	].includes(ext);
}

export function isLocalSqliteFile(uriOrPath: string): boolean {
	return isClientUploadableFile(uriOrPath);
}

export async function stageCasBlob(
	client: RemoteHttpClient,
	filePathOrUri: string,
): Promise<string> {
	let rawPath = filePathOrUri
		.replace(/^sqlite:\/\/\/?/i, "")
		.replace(/^file:\/\/\/?/i, "")
		.replace(/^duckdb:\/\/\/?/i, "");
	if (/^\/[a-zA-Z]:/.test(rawPath)) {
		rawPath = rawPath.slice(1);
	}
	if (!existsSync(rawPath)) {
		throw new CliDomainError(
			"RESOURCE_NOT_FOUND",
			`Local file to upload does not exist: '${rawPath}'`,
			404,
		);
	}
	let buf: Buffer;
	try {
		buf = readFileSync(rawPath);
	} catch (err) {
		throw new CliDomainError(
			"SOURCE_LOAD_ERROR",
			`Failed to read local file '${rawPath}': ${err}`,
			400,
		);
	}
	const hash = createHash("sha256").update(buf).digest("hex").toLowerCase();
	let res: Response;
	try {
		res = await client.fetchImpl(`${client.baseUrl}/api/v1/cas/blobs`, {
			method: "POST",
			headers: {
				"Content-Type": "application/octet-stream",
				"X-CAS-SHA256": hash,
				...client.getHeaders(),
			},
			body: buf,
		});
	} catch (netErr) {
		throw new CliDomainError(
			"INTERNAL_ERROR",
			`Network error while uploading binary file '${rawPath}' to CAS: ${netErr}`,
			500,
		);
	}
	if (!res.ok) {
		const errText = await res.text().catch(() => "");
		throw new CliDomainError(
			"INTERNAL_ERROR",
			`Failed to upload binary file '${rawPath}' to CAS (${res.status}): ${errText}`,
			res.status,
		);
	}
	return hash;
}
