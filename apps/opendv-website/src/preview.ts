import { existsSync, statSync } from "node:fs";
import { extname, join, resolve, sep } from "node:path";
import { getBasePath } from "./lib/base-path";

export interface PreviewServerOptions {
	readonly port?: number | undefined;
	readonly distDir?: string | undefined;
	readonly basePath?: string | undefined;
}

const MIME_TYPES: Record<string, string> = {
	".html": "text/html; charset=utf-8",
	".css": "text/css; charset=utf-8",
	".js": "text/javascript; charset=utf-8",
	".mjs": "text/javascript; charset=utf-8",
	".json": "application/json; charset=utf-8",
	".woff2": "font/woff2",
	".webp": "image/webp",
	".png": "image/png",
	".svg": "image/svg+xml; charset=utf-8",
	".mp4": "video/mp4",
	".webm": "video/webm",
	".vtt": "text/vtt; charset=utf-8",
	".srt": "application/x-subrip; charset=utf-8",
	".txt": "text/plain; charset=utf-8",
	".xml": "application/xml; charset=utf-8",
	".ico": "image/x-icon",
};

function getMimeType(filePath: string): string {
	const ext = extname(filePath).toLowerCase();
	return MIME_TYPES[ext] ?? "application/octet-stream";
}

function resolveDistFile(
	distDir: string,
	relativeUrlPath: string,
): string | null {
	const cleaned = relativeUrlPath.replace(/^\/+/, "");
	const candidate = resolve(distDir, cleaned);
	if (candidate !== distDir && !candidate.startsWith(`${distDir}${sep}`)) {
		return null;
	}

	if (cleaned === "") {
		const indexPath = join(distDir, "index.html");
		return existsSync(indexPath) ? indexPath : null;
	}

	if (existsSync(candidate)) {
		const stats = statSync(candidate);
		if (stats.isFile()) {
			return candidate;
		}
		if (stats.isDirectory()) {
			const nestedIndex = join(candidate, "index.html");
			return existsSync(nestedIndex) ? nestedIndex : null;
		}
	}

	if (!extname(candidate)) {
		const nestedIndex = join(candidate, "index.html");
		if (existsSync(nestedIndex)) {
			return nestedIndex;
		}
		const htmlSibling = `${candidate}.html`;
		if (existsSync(htmlSibling)) {
			return htmlSibling;
		}
	}

	return null;
}

function serveFileWithRange(
	request: Request,
	filePath: string,
	statusOverride?: number,
): Response {
	const stats = statSync(filePath);
	const size = stats.size;
	const contentType = getMimeType(filePath);
	const isHtml = extname(filePath).toLowerCase() === ".html";
	const cacheControl = isHtml ? "no-cache" : "public, max-age=3600";
	const isHead = request.method === "HEAD";

	const rangeHeader = request.headers.get("range");
	if (rangeHeader && statusOverride === undefined) {
		const match = /^bytes=(\d*)-(\d*)$/i.exec(rangeHeader.trim());
		if (!match) {
			return new Response(null, {
				status: 416,
				headers: { "Content-Range": `bytes */${size}` },
			});
		}

		const startStr = match[1] ?? "";
		const endStr = match[2] ?? "";
		if (startStr === "" && endStr === "") {
			return new Response(null, {
				status: 416,
				headers: { "Content-Range": `bytes */${size}` },
			});
		}

		let start: number;
		let end: number;
		if (startStr === "") {
			const suffixLen = Number(endStr);
			if (Number.isNaN(suffixLen) || suffixLen <= 0 || size === 0) {
				return new Response(null, {
					status: 416,
					headers: { "Content-Range": `bytes */${size}` },
				});
			}
			start = Math.max(0, size - suffixLen);
			end = size - 1;
		} else {
			start = Number(startStr);
			end = endStr === "" ? size - 1 : Math.min(Number(endStr), size - 1);
		}

		if (
			Number.isNaN(start) ||
			Number.isNaN(end) ||
			start < 0 ||
			start >= size ||
			end < start
		) {
			return new Response(null, {
				status: 416,
				headers: { "Content-Range": `bytes */${size}` },
			});
		}

		const length = end - start + 1;
		const body = isHead ? null : Bun.file(filePath).slice(start, end + 1);
		return new Response(body, {
			status: 206,
			headers: {
				"Content-Type": contentType,
				"Content-Length": String(length),
				"Content-Range": `bytes ${start}-${end}/${size}`,
				"Accept-Ranges": "bytes",
				"Cache-Control": cacheControl,
			},
		});
	}

	const status = statusOverride ?? 200;
	const body = isHead ? null : Bun.file(filePath);
	return new Response(body, {
		status,
		headers: {
			"Content-Type": contentType,
			"Content-Length": String(size),
			"Accept-Ranges": "bytes",
			"Cache-Control": cacheControl,
		},
	});
}

function renderNotFound(request: Request, distDir: string): Response {
	const notFoundFile = join(distDir, "404.html");
	if (existsSync(notFoundFile)) {
		return serveFileWithRange(request, notFoundFile, 404);
	}
	return new Response(request.method === "HEAD" ? null : "404 Not Found", {
		status: 404,
		headers: { "Content-Type": "text/plain; charset=utf-8" },
	});
}

export function startPreviewServer(
	options: PreviewServerOptions = {},
): ReturnType<typeof Bun.serve> {
	const appRoot = resolve(import.meta.dir, "..");
	const distDir = resolve(options.distDir ?? join(appRoot, "dist"));
	const basePath = getBasePath(options.basePath);
	const port = options.port ?? Number(process.env.PORT ?? 4173);

	return Bun.serve({
		hostname: "127.0.0.1",
		port,
		fetch(request: Request): Response {
			if (request.method !== "GET" && request.method !== "HEAD") {
				return new Response("Method Not Allowed", {
					status: 405,
					headers: { Allow: "GET, HEAD" },
				});
			}

			let pathname: string;
			try {
				pathname = decodeURIComponent(new URL(request.url).pathname);
			} catch {
				return renderNotFound(request, distDir);
			}

			if (basePath !== "") {
				if (pathname === "/" || pathname === basePath) {
					return new Response(null, {
						status: 302,
						headers: { Location: `${basePath}/` },
					});
				}
				if (!pathname.startsWith(`${basePath}/`)) {
					return renderNotFound(request, distDir);
				}
			}

			const strippedPath =
				basePath === "" ? pathname : pathname.slice(basePath.length);
			const targetFile = resolveDistFile(distDir, strippedPath);
			if (!targetFile) {
				return renderNotFound(request, distDir);
			}

			return serveFileWithRange(request, targetFile);
		},
	});
}

if (import.meta.main) {
	const server = startPreviewServer();
	const basePath = getBasePath();
	const suffix = basePath === "" ? "/" : `${basePath}/`;
	console.log(`Previewing open-dv at http://127.0.0.1:${server.port}${suffix}`);
}
