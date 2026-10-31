/**
 * Semantic Theme Registry (`ThemeRegistry`) for the Open-DV Explainability Workbench (`docs/10` §3.1).
 *
 * Supports:
 * - Built-in presets: `obsidian` ("Obsidian Dark"), `paper` ("Daylight Paper"), `high-contrast` ("High-Contrast Audit" WCAG AAA)
 * - Extensible custom theme registration (`registerTheme`)
 * - Persistence to `localStorage` under `open-dv-active-theme`
 * - Zero-FOUC DOM `document.documentElement.setAttribute('data-theme', theme)` synchronization
 * - WCAG AA/AAA Dual-Encoded Entity & Verdict Visual Grammar (`docs/06` §2.2)
 */

import type { VerdictStatus } from "@opendv/protocol-ts";

export const THEME_STORAGE_KEY = "open-dv-active-theme";

export type PresetThemeId = "obsidian" | "paper" | "high-contrast";

export type ThemeId = PresetThemeId | (string & {});

export type ThemeContrastStandard = "WCAG_AA" | "WCAG_AAA";

export type ThemeColorScheme = "dark" | "light" | "high-contrast";

export const REQUIRED_DV_THEME_TOKENS = [
	"--dv-bg-canvas",
	"--dv-surface-1",
	"--dv-surface-2",
	"--dv-surface-3",
	"--dv-border-subtle",
	"--dv-border-strong",
	"--dv-text-primary",
	"--dv-text-secondary",
	"--dv-text-muted",
	"--dv-text-inverse",
	"--dv-hub-accent",
	"--dv-lnk-accent",
	"--dv-sal-accent",
	"--dv-sat-accent",
	"--dv-sat-pii-accent",
	"--dv-ref-accent",
	"--dv-surrogate-accent",
	"--dv-gate-pass",
	"--dv-gate-autofix",
	"--dv-gate-reject",
	"--dv-gate-askuser",
] as const;

export type DvThemeTokenName = (typeof REQUIRED_DV_THEME_TOKENS)[number];

export type ThemeTokenMap = Record<DvThemeTokenName, string>;

export interface ThemeDefinition {
	id: string;
	label: string;
	name: string;
	description: string;
	colorScheme: ThemeColorScheme;
	contrastStandard: ThemeContrastStandard;
	isPreset: boolean;
	tokens: ThemeTokenMap;
}

export interface CustomThemeRegistrationInput {
	id: string;
	label: string;
	name?: string | undefined;
	description?: string | undefined;
	colorScheme?: ThemeColorScheme | undefined;
	contrastStandard?: ThemeContrastStandard | undefined;
	tokens: Partial<ThemeTokenMap> &
		Pick<ThemeTokenMap, "--dv-bg-canvas" | "--dv-text-primary">;
}

export const OBSIDIAN_THEME: ThemeDefinition = {
	id: "obsidian",
	label: "Obsidian Dark",
	name: "Obsidian Dark",
	description:
		"Default low-glare dark theme engineered for long Data Vault 2.0 topology & audit sessions.",
	colorScheme: "dark",
	contrastStandard: "WCAG_AA",
	isPreset: true,
	tokens: {
		"--dv-bg-canvas": "#0b0f19",
		"--dv-surface-1": "#111827",
		"--dv-surface-2": "#1f2937",
		"--dv-surface-3": "#374151",
		"--dv-border-subtle": "#1f293d",
		"--dv-border-strong": "#374151",
		"--dv-text-primary": "#f9fafb",
		"--dv-text-secondary": "#9ca3af",
		"--dv-text-muted": "#6b7280",
		"--dv-text-inverse": "#111827",
		"--dv-hub-accent": "#3b82f6",
		"--dv-lnk-accent": "#10b981",
		"--dv-sal-accent": "#14b8a6",
		"--dv-sat-accent": "#f59e0b",
		"--dv-sat-pii-accent": "#a855f7",
		"--dv-ref-accent": "#64748b",
		"--dv-surrogate-accent": "#475569",
		"--dv-gate-pass": "#10b981",
		"--dv-gate-autofix": "#0284c7",
		"--dv-gate-reject": "#ef4444",
		"--dv-gate-askuser": "#f59e0b",
	},
};

export const PAPER_THEME: ThemeDefinition = {
	id: "paper",
	label: "Daylight Paper",
	name: "Daylight Paper",
	description:
		"Crisp high-legibility light theme for executive reviews, printed audit reports, and bright rooms.",
	colorScheme: "light",
	contrastStandard: "WCAG_AA",
	isPreset: true,
	tokens: {
		"--dv-bg-canvas": "#f8fafc",
		"--dv-surface-1": "#ffffff",
		"--dv-surface-2": "#f1f5f9",
		"--dv-surface-3": "#e2e8f0",
		"--dv-border-subtle": "#e2e8f0",
		"--dv-border-strong": "#cbd5e1",
		"--dv-text-primary": "#0f172a",
		"--dv-text-secondary": "#475569",
		"--dv-text-muted": "#64748b",
		"--dv-text-inverse": "#ffffff",
		"--dv-hub-accent": "#2563eb",
		"--dv-lnk-accent": "#059669",
		"--dv-sal-accent": "#0f766e",
		"--dv-sat-accent": "#d97706",
		"--dv-sat-pii-accent": "#7e22ce",
		"--dv-ref-accent": "#475569",
		"--dv-surrogate-accent": "#64748b",
		"--dv-gate-pass": "#16a34a",
		"--dv-gate-autofix": "#0284c7",
		"--dv-gate-reject": "#dc2626",
		"--dv-gate-askuser": "#d97706",
	},
};

export const HIGH_CONTRAST_THEME: ThemeDefinition = {
	id: "high-contrast",
	label: "High-Contrast Audit",
	name: "High-Contrast Audit",
	description:
		"WCAG AAA (>= 7:1 contrast ratio) accessibility theme with pure-black canvas and solid white borders.",
	colorScheme: "high-contrast",
	contrastStandard: "WCAG_AAA",
	isPreset: true,
	tokens: {
		"--dv-bg-canvas": "#000000",
		"--dv-surface-1": "#0f172a",
		"--dv-surface-2": "#1e293b",
		"--dv-surface-3": "#334155",
		"--dv-border-subtle": "#ffffff",
		"--dv-border-strong": "#ffffff",
		"--dv-text-primary": "#ffffff",
		"--dv-text-secondary": "#f8fafc",
		"--dv-text-muted": "#cbd5e1",
		"--dv-text-inverse": "#000000",
		"--dv-hub-accent": "#60a5fa",
		"--dv-lnk-accent": "#34d399",
		"--dv-sal-accent": "#2dd4bf",
		"--dv-sat-accent": "#fbbf24",
		"--dv-sat-pii-accent": "#c084fc",
		"--dv-ref-accent": "#94a3b8",
		"--dv-surrogate-accent": "#e2e8f0",
		"--dv-gate-pass": "#4ade80",
		"--dv-gate-autofix": "#38bdf8",
		"--dv-gate-reject": "#f87171",
		"--dv-gate-askuser": "#fbbf24",
	},
};

export const PRESET_THEMES: Readonly<Record<PresetThemeId, ThemeDefinition>> = {
	obsidian: OBSIDIAN_THEME,
	paper: PAPER_THEME,
	"high-contrast": HIGH_CONTRAST_THEME,
};

/**
 * Dual-encoded WCAG AA Entity Visual Grammar (`docs/06` §2.2).
 * Never rely on color alone: combines geometric shape, CSS color token, and explicit text prefix.
 */
export type DvEntityKind =
	| "HUB"
	| "LNK"
	| "SAL"
	| "SAT"
	| "SAT_PII"
	| "REF"
	| "DROPPED_SURROGATE";

export interface EntityVisualEncoding {
	kind: DvEntityKind;
	prefixBadge: string;
	geometricShape: string;
	borderStyle: string;
	cssColorVar: DvThemeTokenName;
}

export const ENTITY_VISUAL_GRAMMAR: Readonly<
	Record<DvEntityKind, EntityVisualEncoding>
> = {
	HUB: {
		kind: "HUB",
		prefixBadge: "[HUB]",
		geometricShape: "rounded-rectangle",
		borderStyle: "2px solid",
		cssColorVar: "--dv-hub-accent",
	},
	LNK: {
		kind: "LNK",
		prefixBadge: "[LNK]",
		geometricShape: "diamond",
		borderStyle: "2px solid",
		cssColorVar: "--dv-lnk-accent",
	},
	SAL: {
		kind: "SAL",
		prefixBadge: "[SAL]",
		geometricShape: "double-diamond",
		borderStyle: "2px dashed",
		cssColorVar: "--dv-sal-accent",
	},
	SAT: {
		kind: "SAT",
		prefixBadge: "[SAT]",
		geometricShape: "stadium-pill",
		borderStyle: "1.5px solid",
		cssColorVar: "--dv-sat-accent",
	},
	SAT_PII: {
		kind: "SAT_PII",
		prefixBadge: "[SAT-PII]",
		geometricShape: "octagon-lock",
		borderStyle: "2px dashed",
		cssColorVar: "--dv-sat-pii-accent",
	},
	REF: {
		kind: "REF",
		prefixBadge: "[REF]",
		geometricShape: "flat-hexagon",
		borderStyle: "1.5px dotted",
		cssColorVar: "--dv-ref-accent",
	},
	DROPPED_SURROGATE: {
		kind: "DROPPED_SURROGATE",
		prefixBadge: "[DROPPED-SURROGATE]",
		geometricShape: "crossed-sink",
		borderStyle: "1.5px dashed",
		cssColorVar: "--dv-surrogate-accent",
	},
};

/**
 * Dual-encoded WCAG AA Verdict Visual Grammar (`docs/06` §2.2).
 */
export interface VerdictVisualEncoding {
	status: VerdictStatus;
	prefixBadge: string;
	shapeSymbol: string;
	cssColorVar: DvThemeTokenName;
}

export const VERDICT_VISUAL_GRAMMAR: Readonly<
	Record<VerdictStatus, VerdictVisualEncoding>
> = {
	PASS: {
		status: "PASS",
		prefixBadge: "[PASS]",
		shapeSymbol: "●",
		cssColorVar: "--dv-gate-pass",
	},
	"AUTO-FIX": {
		status: "AUTO-FIX",
		prefixBadge: "[AUTO-FIX]",
		shapeSymbol: "◆",
		cssColorVar: "--dv-gate-autofix",
	},
	AUTO_REPAIRED: {
		status: "AUTO_REPAIRED",
		prefixBadge: "[AUTO_REPAIRED]",
		shapeSymbol: "◆",
		cssColorVar: "--dv-gate-autofix",
	},
	"REJECT-WITH-TRACE": {
		status: "REJECT-WITH-TRACE",
		prefixBadge: "[REJECT-WITH-TRACE]",
		shapeSymbol: "■",
		cssColorVar: "--dv-gate-reject",
	},
	"ASK-USER": {
		status: "ASK-USER",
		prefixBadge: "[ASK-USER]",
		shapeSymbol: "▲",
		cssColorVar: "--dv-gate-askuser",
	},
};

export interface ThemeStorageLike {
	getItem(key: string): string | null;
	setItem(key: string, value: string): void;
	removeItem?(key: string): void;
}

export type ThemeChangeListener = (theme: ThemeDefinition) => void;

const SAFE_THEME_ID_PATTERN = /^[a-z0-9-]+$/i;

function resolveDefaultStorage(): ThemeStorageLike | null {
	try {
		if (
			typeof globalThis !== "undefined" &&
			"localStorage" in globalThis &&
			globalThis.localStorage &&
			typeof globalThis.localStorage.getItem === "function" &&
			typeof globalThis.localStorage.setItem === "function"
		) {
			return globalThis.localStorage;
		}
	} catch {
		// Ignore restricted storage environments
	}
	return null;
}

export class ThemeRegistry {
	private readonly themes = new Map<string, ThemeDefinition>();
	private readonly listeners = new Set<ThemeChangeListener>();
	private readonly fallbackMemoryStorage = new Map<string, string>();
	private customStorage: ThemeStorageLike | null = null;
	private activeThemeId = "obsidian";

	constructor(storage?: ThemeStorageLike) {
		if (storage) {
			this.customStorage = storage;
		}
		this.loadPresetThemes();
	}

	private loadPresetThemes(): void {
		this.themes.set(OBSIDIAN_THEME.id, OBSIDIAN_THEME);
		this.themes.set(PAPER_THEME.id, PAPER_THEME);
		this.themes.set(HIGH_CONTRAST_THEME.id, HIGH_CONTRAST_THEME);
	}

	private getStorage(): ThemeStorageLike {
		if (this.customStorage) {
			return this.customStorage;
		}
		const browserStorage = resolveDefaultStorage();
		if (browserStorage) {
			return browserStorage;
		}
		return {
			getItem: (key: string) => this.fallbackMemoryStorage.get(key) ?? null,
			setItem: (key: string, val: string) => {
				this.fallbackMemoryStorage.set(key, val);
			},
			removeItem: (key: string) => {
				this.fallbackMemoryStorage.delete(key);
			},
		};
	}

	getTheme(themeId: string): ThemeDefinition | undefined {
		return this.themes.get(themeId);
	}

	listThemes(): ThemeDefinition[] {
		return Array.from(this.themes.values());
	}

	registerTheme(input: CustomThemeRegistrationInput): ThemeDefinition {
		const trimmedId = input.id.trim();
		if (!trimmedId || !SAFE_THEME_ID_PATTERN.test(trimmedId)) {
			throw new Error(
				`Invalid theme id '${input.id}'. Must match /^[a-z0-9-]+$/i.`,
			);
		}
		const trimmedLabel = (input.label || input.name || "").trim();
		if (!trimmedLabel) {
			throw new Error(`Theme '${trimmedId}' must provide a non-empty label.`);
		}

		const baseTokens =
			input.colorScheme === "light"
				? PAPER_THEME.tokens
				: input.colorScheme === "high-contrast"
					? HIGH_CONTRAST_THEME.tokens
					: OBSIDIAN_THEME.tokens;

		const mergedTokens: ThemeTokenMap = {
			...baseTokens,
			...(input.tokens as Partial<ThemeTokenMap>),
		};

		for (const tokenName of REQUIRED_DV_THEME_TOKENS) {
			const val = mergedTokens[tokenName];
			if (!val || typeof val !== "string" || val.trim().length === 0) {
				throw new Error(
					`Theme '${trimmedId}' is missing required CSS custom property '${tokenName}'.`,
				);
			}
		}

		const definition: ThemeDefinition = {
			id: trimmedId,
			label: trimmedLabel,
			name: input.name?.trim() || trimmedLabel,
			description:
				input.description?.trim() ||
				`Custom registered theme (${trimmedLabel})`,
			colorScheme: input.colorScheme ?? "dark",
			contrastStandard: input.contrastStandard ?? "WCAG_AA",
			isPreset: false,
			tokens: mergedTokens,
		};

		this.themes.set(trimmedId, definition);
		if (this.activeThemeId === trimmedId) {
			this.syncDomTheme(definition);
		}
		return definition;
	}

	getActiveThemeId(): string {
		return this.activeThemeId;
	}

	getActiveTheme(): ThemeDefinition {
		return this.themes.get(this.activeThemeId) ?? OBSIDIAN_THEME;
	}

	setActiveTheme(themeId: string): ThemeDefinition {
		const theme = this.themes.get(themeId);
		if (!theme) {
			throw new Error(
				`Unknown theme '${themeId}'. Registered themes: ${Array.from(this.themes.keys()).join(", ")}`,
			);
		}
		this.activeThemeId = theme.id;
		try {
			this.getStorage().setItem(THEME_STORAGE_KEY, theme.id);
		} catch {
			// Ignore storage quota / security errors
		}
		this.syncDomTheme(theme);
		for (const listener of this.listeners) {
			listener(theme);
		}
		return theme;
	}

	applyTheme(themeId: string): ThemeDefinition {
		return this.setActiveTheme(themeId);
	}

	initializeFromEnvironment(): ThemeDefinition {
		let resolvedId = "obsidian";
		try {
			const saved = this.getStorage().getItem(THEME_STORAGE_KEY);
			if (saved && this.themes.has(saved)) {
				resolvedId = saved;
			} else if (
				typeof globalThis !== "undefined" &&
				"matchMedia" in globalThis &&
				typeof globalThis.matchMedia === "function"
			) {
				if (globalThis.matchMedia("(prefers-contrast: more)").matches) {
					resolvedId = "high-contrast";
				} else if (
					globalThis.matchMedia("(prefers-color-scheme: light)").matches
				) {
					resolvedId = "paper";
				}
			}
		} catch {
			resolvedId = "obsidian";
		}
		return this.setActiveTheme(resolvedId);
	}

	private syncDomTheme(theme: ThemeDefinition): void {
		if (
			typeof globalThis === "undefined" ||
			!("document" in globalThis) ||
			!globalThis.document?.documentElement
		) {
			return;
		}
		const rootEl = globalThis.document.documentElement;
		rootEl.setAttribute("data-theme", theme.id);
		if (rootEl.style && typeof rootEl.style.setProperty === "function") {
			for (const tokenName of REQUIRED_DV_THEME_TOKENS) {
				rootEl.style.setProperty(tokenName, theme.tokens[tokenName]);
			}
		}
	}

	subscribe(listener: ThemeChangeListener): () => void {
		this.listeners.add(listener);
		return () => {
			this.listeners.delete(listener);
		};
	}

	resetForTesting(): void {
		this.themes.clear();
		this.loadPresetThemes();
		this.fallbackMemoryStorage.clear();
		try {
			this.getStorage().removeItem?.(THEME_STORAGE_KEY);
		} catch {
			// Ignore
		}
		this.activeThemeId = "obsidian";
		this.syncDomTheme(OBSIDIAN_THEME);
	}

	// Static façade delegating to the default singleton instance for ergonomic global access
	static getTheme(themeId: string): ThemeDefinition | undefined {
		return defaultThemeRegistry.getTheme(themeId);
	}

	static listThemes(): ThemeDefinition[] {
		return defaultThemeRegistry.listThemes();
	}

	static registerTheme(input: CustomThemeRegistrationInput): ThemeDefinition {
		return defaultThemeRegistry.registerTheme(input);
	}

	static getActiveThemeId(): string {
		return defaultThemeRegistry.getActiveThemeId();
	}

	static getActiveTheme(): ThemeDefinition {
		return defaultThemeRegistry.getActiveTheme();
	}

	static setActiveTheme(themeId: string): ThemeDefinition {
		return defaultThemeRegistry.setActiveTheme(themeId);
	}

	static applyTheme(themeId: string): ThemeDefinition {
		return defaultThemeRegistry.applyTheme(themeId);
	}

	static initializeFromEnvironment(): ThemeDefinition {
		return defaultThemeRegistry.initializeFromEnvironment();
	}

	static subscribe(listener: ThemeChangeListener): () => void {
		return defaultThemeRegistry.subscribe(listener);
	}

	static resetForTesting(): void {
		defaultThemeRegistry.resetForTesting();
	}
}

export const defaultThemeRegistry = new ThemeRegistry();
export const themeRegistry = defaultThemeRegistry;
