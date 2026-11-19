/**
 * Open-access loopback fetch, URL resolution, role normalization, and JCS SHA-256 helpers.
 * (apps/opendv-web-workbench/src/ontology/store/studio-fetch.ts)
 */

import { dumpsJcs } from "./jcs-export";
import type { StudioUserRole } from "./types";

export function normalizeBaseApiUrl(rawBaseApiUrl?: string): string {
	const raw =
		rawBaseApiUrl ??
		(typeof process !== "undefined" && process.env?.OPENDV_API_URL
			? process.env.OPENDV_API_URL
			: "");
	let urlEnd = raw.length;
	while (urlEnd > 0 && raw.charCodeAt(urlEnd - 1) === 47) {
		urlEnd--;
	}
	return raw.slice(0, urlEnd);
}

export function resolveStudioUrl(baseApiUrl: string, path: string): string {
	const cleanPath = path.startsWith("/") ? path : `/${path}`;
	return baseApiUrl ? `${baseApiUrl}${cleanPath}` : cleanPath;
}

export function normalizeStudioRole(
	rawRole?: unknown,
	rawPrincipal?: unknown,
): StudioUserRole | undefined {
	if (typeof rawRole === "string") {
		const cleaned = rawRole.trim().toLowerCase();
		if (
			cleaned === "viewer" ||
			cleaned === "ontology_viewer" ||
			cleaned === "read_only"
		) {
			return "viewer";
		}
		if (
			cleaned === "editor" ||
			cleaned === "ontology_editor" ||
			cleaned === "steward" ||
			cleaned === "ontology_steward"
		) {
			return "editor";
		}
		if (
			cleaned === "automated" ||
			cleaned === "ontology_automated" ||
			cleaned === "agent"
		) {
			return "automated";
		}
		if (
			cleaned === "admin" ||
			cleaned === "ontology_admin" ||
			cleaned === "platform_admin"
		) {
			return "admin";
		}
	}
	if (typeof rawPrincipal === "string") {
		const p = rawPrincipal.trim().toLowerCase();
		if (p.startsWith("agent:")) return "automated";
		if (p === "human:local_admin" || p === "human:local_user") return "admin";
	}
	return undefined;
}

export async function computeJcsSha256Hex(
	value: unknown,
): Promise<string | undefined> {
	if (typeof crypto === "undefined" || !crypto.subtle) return undefined;
	try {
		const jcs = dumpsJcs(value);
		const buf = await crypto.subtle.digest(
			"SHA-256",
			new TextEncoder().encode(jcs),
		);
		return Array.from(new Uint8Array(buf))
			.map((b) => b.toString(16).padStart(2, "0"))
			.join("");
	} catch {
		return undefined;
	}
}

export function getStudioAuthHeaders(
	extraHeaders?: Record<string, string>,
	_targetUrl?: string,
): Record<string, string> {
	const {
		"x-is-studio": _xIsStudio,
		"X-OpenDV-Role": _xOpendvRole,
		...headers
	} = extraHeaders ?? {};
	return headers;
}

export type StudioFetcher = (
	fetchImpl: typeof fetch,
	path: string,
	init?: RequestInit,
) => Promise<Response>;

export function createStudioFetcher(
	baseApiUrl: string,
	getRole: () => StudioUserRole,
	setRole: (role: StudioUserRole) => void,
): StudioFetcher {
	return async (fetchImpl, path, init) => {
		const headers: Record<string, string> = {};
		if (init?.headers) {
			if (typeof Headers !== "undefined" && init.headers instanceof Headers) {
				init.headers.forEach((v, k) => {
					headers[k] = v;
				});
			} else if (Array.isArray(init.headers)) {
				for (const [k, v] of init.headers) {
					headers[k] = v;
				}
			} else {
				Object.assign(headers, init.headers as Record<string, string>);
			}
		}

		const nextInit: RequestInit = {
			...init,
			...(init?.method ? { method: init.method } : {}),
			credentials: "include",
			headers,
		};

		const res = await fetchImpl(resolveStudioUrl(baseApiUrl, path), nextInit);
		const roleHeader = res?.headers?.get?.("X-OpenDV-Role");
		if (roleHeader) {
			const hydratedRole = normalizeStudioRole(roleHeader);
			if (hydratedRole && hydratedRole !== getRole()) {
				setRole(hydratedRole);
			}
		}
		return res;
	};
}
