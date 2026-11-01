import { existsSync, readFileSync } from "node:fs";
import { join, resolve } from "node:path";

export const DEFAULT_DEV_SERVER_PORT = 5173;
export const DEFAULT_DEV_SERVER_HOST = "127.0.0.1";
export const DEFAULT_API_URL = "http://127.0.0.1:8420";
export const WORKBENCH_CSP =
	"default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; frame-ancestors 'self'";

export interface WorkbenchDevServerOptions {
	/** Port to listen on. Defaults to process.env.PORT ?? 5173. Use 0 for an ephemeral port in tests. */
	port?: number;
	/** Upstream Platform API URL. Defaults to process.env.OPENDV_API_URL ?? "http://127.0.0.1:8420". */
	apiUrl?: string;
	/** Whether to suppress listening / proxy startup log messages. */
	silent?: boolean;
}

export type WorkbenchDevServer = ReturnType<typeof Bun.serve>;

async function buildBrowserBundle(workbenchRoot: string): Promise<string> {
	const entrypoint = join(workbenchRoot, "src/index.ts");
	const buildResult = await Bun.build({
		entrypoints: [entrypoint],
		target: "browser",
	});
	if (!buildResult.success) {
		const errorMsg = buildResult.logs.map((l) => l.message).join("\n");
		throw new Error(`Failed to compile browser bundle: ${errorMsg}`);
	}
	const firstOutput = buildResult.outputs[0];
	if (!firstOutput) {
		throw new Error("Bun.build produced no outputs");
	}
	return await firstOutput.text();
}

/**
 * Construct and launch the zero-dependency Bun development server for `@opendv/web-workbench`.
 *
 * Guarantees:
 * - Binds strictly to `127.0.0.1` (default port 5173, port 0 supported for testing).
 * - Serves `index.html` at `/` and `/index.html` with CSP headers.
 * - Serves `/src/index.css` with text/css and CSP headers.
 * - Compiles and serves the browser bundle at `/src/index.ts` with application/javascript and CSP headers.
 * - Proxies `/api/*` requests to the upstream Platform API server (default `http://127.0.0.1:8420`),
 *   returning RFC 9457 `502 Bad Gateway` problem details if upstream is unreachable.
 */
export function createWorkbenchDevServer(
	options: WorkbenchDevServerOptions = {},
): WorkbenchDevServer {
	const envPort = process.env.PORT
		? Number.parseInt(process.env.PORT, 10)
		: undefined;
	const port =
		options.port !== undefined
			? options.port
			: envPort !== undefined && !Number.isNaN(envPort)
				? envPort
				: DEFAULT_DEV_SERVER_PORT;
	const hostname = DEFAULT_DEV_SERVER_HOST;
	const apiUrl =
		options.apiUrl ?? process.env.OPENDV_API_URL ?? DEFAULT_API_URL;
	const workbenchRoot = resolve(import.meta.dir, "..");

	let cachedBundle: string | null = null;
	async function getBundle(): Promise<string> {
		if (cachedBundle) {
			return cachedBundle;
		}
		cachedBundle = await buildBrowserBundle(workbenchRoot);
		return cachedBundle;
	}

	return Bun.serve({
		port,
		hostname,
		async fetch(request) {
			const url = new URL(request.url);

			// 1. Proxy /api and /api/* requests to upstream Platform API
			if (url.pathname === "/api" || url.pathname.startsWith("/api/")) {
				const targetUrl = `${apiUrl.replace(/\/+$/, "")}${url.pathname}${url.search}`;
				const headers = new Headers();
				for (const [key, value] of request.headers.entries()) {
					if (key.toLowerCase() !== "host") {
						headers.set(key, value);
					}
				}
				const init: RequestInit = {
					method: request.method,
					headers,
				};
				if (request.method !== "GET" && request.method !== "HEAD") {
					init.body = await request.arrayBuffer();
				}
				try {
					const upstreamResp = await fetch(targetUrl, init);
					const respHeaders = new Headers(upstreamResp.headers);
					return new Response(upstreamResp.body, {
						status: upstreamResp.status,
						headers: respHeaders,
					});
				} catch (error) {
					const detail = `Upstream Platform API at ${apiUrl} is unreachable: ${error instanceof Error ? error.message : String(error)}`;
					return new Response(
						JSON.stringify({
							type: "https://open-dv.dev/problems/http-502",
							title: "Bad Gateway",
							status: 502,
							detail,
							instance: url.pathname + url.search,
						}),
						{
							status: 502,
							headers: {
								"Content-Type": "application/problem+json; charset=utf-8",
								"Content-Security-Policy": WORKBENCH_CSP,
							},
						},
					);
				}
			}

			// 2. Serve index.html at / or /index.html
			if (url.pathname === "/" || url.pathname === "/index.html") {
				const htmlPath = join(workbenchRoot, "index.html");
				if (existsSync(htmlPath)) {
					const html = readFileSync(htmlPath, "utf-8");
					return new Response(html, {
						status: 200,
						headers: {
							"Content-Type": "text/html; charset=utf-8",
							"Content-Security-Policy": WORKBENCH_CSP,
						},
					});
				}
			}

			// 3. Serve CSS at /src/index.css
			if (url.pathname === "/src/index.css") {
				const cssPath = join(workbenchRoot, "src/index.css");
				if (existsSync(cssPath)) {
					const css = readFileSync(cssPath, "utf-8");
					return new Response(css, {
						status: 200,
						headers: {
							"Content-Type": "text/css; charset=utf-8",
							"Content-Security-Policy": WORKBENCH_CSP,
						},
					});
				}
			}

			// 4. Serve compiled browser JS bundle at /src/index.ts
			if (url.pathname === "/src/index.ts") {
				try {
					const js = await getBundle();
					return new Response(js, {
						status: 200,
						headers: {
							"Content-Type": "application/javascript; charset=utf-8",
							"Content-Security-Policy": WORKBENCH_CSP,
						},
					});
				} catch (error) {
					return new Response(
						`Build Error: ${error instanceof Error ? error.message : String(error)}`,
						{
							status: 500,
							headers: {
								"Content-Type": "text/plain; charset=utf-8",
								"Content-Security-Policy": WORKBENCH_CSP,
							},
						},
					);
				}
			}

			// 5. Not Found
			return new Response("Not Found", {
				status: 404,
				headers: {
					"Content-Type": "text/plain; charset=utf-8",
					"Content-Security-Policy": WORKBENCH_CSP,
				},
			});
		},
	});
}

/**
 * Launch the workbench development server and log listening and proxy configuration.
 */
export function startWorkbenchDevServer(
	options: WorkbenchDevServerOptions = {},
): WorkbenchDevServer {
	const server = createWorkbenchDevServer(options);
	const apiUrl =
		options.apiUrl ?? process.env.OPENDV_API_URL ?? DEFAULT_API_URL;
	if (!options.silent) {
		console.log(
			`Open-DV Web Workbench dev server running at http://${server.hostname}:${server.port}`,
		);
		console.log(`Proxying /api requests to ${apiUrl}`);
	}
	return server;
}

if (import.meta.main) {
	startWorkbenchDevServer();
}
