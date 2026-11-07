/**
 * Vite 6 configuration for `@opendv/web-workbench`.
 *
 * Security & Decoupling Guarantees:
 * - Binds local development server strictly to `127.0.0.1` (`localhost`) on port `5173`.
 * - Proxies `/api` requests to the headless FastAPI Platform Server (`http://127.0.0.1:8000`).
 * - Resolves `@opendv/protocol-ts` workspace SDK without any backend Python dependencies.
 */

export interface WorkbenchViteProxyRule {
	target: string;
	changeOrigin: boolean;
}

export interface WorkbenchViteConfig {
	root: string;
	server: {
		host: "127.0.0.1" | "localhost";
		port: number;
		strictPort: boolean;
		proxy: Record<string, WorkbenchViteProxyRule>;
	};
	resolve: {
		alias: Record<string, string>;
	};
	build: {
		outDir: string;
		target: string;
		sourcemap: boolean;
	};
}

export function defineWorkbenchViteConfig(
	config: WorkbenchViteConfig,
): WorkbenchViteConfig {
	return config;
}

export const viteConfig: WorkbenchViteConfig = defineWorkbenchViteConfig({
	root: ".",
	server: {
		host: "127.0.0.1",
		port: 5173,
		strictPort: true,
		proxy: {
			"/api": {
				target: "http://127.0.0.1:8000",
				changeOrigin: true,
			},
		},
	},
	resolve: {
		alias: {
			"@opendv/protocol-ts": "../../packages/opendv-protocol-ts/src/index.ts",
		},
	},
	build: {
		outDir: "dist",
		target: "es2022",
		sourcemap: true,
	},
});

export default viteConfig;
