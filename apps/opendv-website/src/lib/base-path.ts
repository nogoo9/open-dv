const DEFAULT_BASE_PATH = "/open-dv";

const EXTERNAL_SCHEME_RE = /^(?:https?:|\/\/|#|mailto:)/i;

/**
 * Resolve and normalize the static deployment base path from `OPENDV_SITE_BASE_PATH`.
 * Defaults to `"/open-dv"` when unset, and collapses `"/"` or whitespace to `""`.
 */
export function getBasePath(override?: string): string {
	const envValue =
		typeof process !== "undefined"
			? process.env.OPENDV_SITE_BASE_PATH
			: undefined;
	const raw = override !== undefined ? override : envValue;
	if (raw === undefined) {
		return DEFAULT_BASE_PATH;
	}
	const wsTrimmed = raw.trim();
	let start = 0;
	let end = wsTrimmed.length;
	while (start < end && wsTrimmed.charCodeAt(start) === 47 /* '/' */) {
		start += 1;
	}
	while (end > start && wsTrimmed.charCodeAt(end - 1) === 47 /* '/' */) {
		end -= 1;
	}
	const trimmed = wsTrimmed.slice(start, end);
	return trimmed === "" ? "" : `/${trimmed}`;
}

/**
 * Prefix a site-relative route or public asset path with the configured base path.
 */
export function withBase(path: string, override?: string): string {
	if (EXTERNAL_SCHEME_RE.test(path)) {
		return path;
	}
	const base = getBasePath(override);
	const normalized = path.startsWith("/") ? path : `/${path}`;
	if (base === "") {
		return normalized;
	}
	if (normalized === "/") {
		return `${base}/`;
	}
	return `${base}${normalized}`;
}
