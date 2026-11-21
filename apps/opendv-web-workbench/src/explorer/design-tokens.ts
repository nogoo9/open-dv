/**
 * Design Tokens, Glyphs, Status Pills & Typography Constants
 * for the Open-DV 3-Level Pipeline Explorer Workbench (`docs/design/open-dv-workbench/HANDOFF.md`).
 */

import type { StatusTag } from "@opendv/protocol-ts";
import type { CSSProperties } from "react";

export const SPIKE_MARK_SVG = `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" style="flex-shrink:0" aria-hidden="true">
  <g stroke="#141413" stroke-width="2.1" stroke-linecap="round">
    <line x1="12" y1="2.5" x2="12" y2="21.5" />
    <line x1="2.5" y1="12" x2="21.5" y2="12" />
    <line x1="5.3" y1="5.3" x2="18.7" y2="18.7" />
    <line x1="18.7" y1="5.3" x2="5.3" y2="18.7" />
  </g>
</svg>`;

export const CLAUDE_TOKENS = {
	colors: {
		primary: "#cc785c",
		primaryActive: "#a9583e",
		primaryDisabled: "#e6dfd8",
		ink: "#141413",
		body: "#3d3d3a",
		bodyStrong: "#252523",
		muted: "#6c6a64",
		mutedSoft: "#8e8b82",
		hairline: "#e6dfd8",
		hairlineSoft: "#ebe6df",
		hairlineStrong: "#d5cdc2",
		canvas: "#faf9f5",
		surfaceSoft: "#f5f0e8",
		surfaceCard: "#efe9de",
		surfaceCreamStrong: "#e8e0d2",
		surfaceDark: "#181715",
		surfaceDarkSoft: "#1f1e1b",
		surfaceDarkElevated: "#252320",
		onPrimary: "#ffffff",
		onDark: "#faf9f5",
		onDarkSoft: "#a09d96",
		accentTeal: "#5db8a6",
		accentAmber: "#e8a55a",
		success: "#5db872",
		warning: "#d4a017",
		error: "#c64545",
	},
	typography: {
		serifFamily: "'Instrument Serif', Georgia, 'Times New Roman', serif",
		sansFamily:
			"'DM Sans', 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
		monoFamily:
			"'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, monospace",
		workbenchH1: {
			fontFamily: "'Instrument Serif', Georgia, 'Times New Roman', serif",
			fontSize: "26px",
			fontWeight: 400,
			lineHeight: 1.08,
			letterSpacing: "-0.45px",
		},
		workbenchH2: {
			fontFamily: "'Instrument Serif', Georgia, 'Times New Roman', serif",
			fontSize: "20px",
			fontWeight: 400,
			lineHeight: 1.15,
			letterSpacing: "-0.3px",
		},
	},
	chrome: {
		topbarHeight: "54px",
		stepperHeight: "44px",
		subbarHeight: "46px",
		footerHeight: "52px",
		drawerWidth: "716px",
	},
} as const;

export const EXPLORER_FONTS = {
	serif: CLAUDE_TOKENS.typography.serifFamily,
	sans: CLAUDE_TOKENS.typography.sansFamily,
	mono: CLAUDE_TOKENS.typography.monoFamily,
} as const;

export const EXPLORER_COLORS = {
	inkPrimary: "#141413",
	inkBody: "#3d3d3a",
	inkMuted: "#6c6a64",
	inkLabel: "#6c6a64",
	bgPage: "#faf9f5",
	bgSurface: "#faf9f5",
	bgSubtle: "#f5f0e8",
	bgWarmStrip: "#efe9de",
	borderLine: "#e6dfd8",
	borderRail: "#d5cdc2",
	borderDashed: "#d5cdc2",
	borderSelected: "#cc785c",
	glyphControl: "#a9583e",
	glyphData: "#cc785c",
	glyphComponent: "#3d3d3a",
	calloutBg: "#efe9de",
	calloutBorder: "#cc785c",
	calloutText: "#a9583e",
	codeBg: "#181715",
	codeFg: "#faf9f5",
	spanTierU1Bg: "#efe9de",
	spanTierU1Ring: "0 0 0 1px #5db8a6",
	spanTierU1Swatch: "#5db8a6",
	spanTierU3Bg: "#f5f0e8",
	spanTierU3Ring: "0 0 0 1px #e8a55a",
	spanTierU3Swatch: "#e8a55a",
} as const;

export const EXPLORER_A11Y = {
	focusRing: "0 0 0 3px rgba(204, 120, 92, 0.15), 0 0 0 1px #cc785c",
	focusOutline: "2px solid #a9583e",
	focusOutlineOffset: "2px",
	minTargetSize: "32px",
	liveRegionPolite: "polite" as const,
	liveRegionAssertive: "assertive" as const,
} as const;

export interface GlyphStyleSpec {
	bg: string;
	border: string;
	radius: string;
	clip: string;
}

export const EXPLORER_GLYPHS: Record<
	"control" | "data" | "setting" | "component",
	GlyphStyleSpec
> = {
	control: {
		bg: "#a9583e",
		border: "none",
		radius: "1px",
		clip: "polygon(50% 0,100% 50%,50% 100%,0 50%)",
	},
	data: {
		bg: "#cc785c",
		border: "none",
		radius: "2px",
		clip: "none",
	},
	setting: {
		bg: "transparent",
		border: "1.5px solid #a9583e",
		radius: "2px",
		clip: "none",
	},
	component: {
		bg: "#3d3d3a",
		border: "none",
		radius: "999px",
		clip: "none",
	},
};

export const EXPLORER_STATUS_TAGS: Record<
	StatusTag,
	{ bg: string; fg: string }
> = {
	PASS: { bg: "#efe9de", fg: "#141413" },
	AUTO_REPAIRED: { bg: "#f5f0e8", fg: "#8f452d" },
	"ASK-USER": { bg: "#f5f0e8", fg: "#7a4e05" },
	"ASK-USER → resolved": { bg: "#efe9de", fg: "#7a4e05" },
	"REJECT-WITH-TRACE": { bg: "#fdf2f2", fg: "#991b1b" },
	"AUTO-FIX": { bg: "#e8e0d2", fg: "#141413" },
};

export const EXPLORER_STATUS_INDICATORS: Record<
	StatusTag,
	{ label: string; symbol: string }
> = {
	PASS: { label: "Pass", symbol: "✓" },
	AUTO_REPAIRED: {
		label: "Auto Repaired",
		symbol: "↺",
	},
	"ASK-USER": {
		label: "Steward Hold",
		symbol: "⚠️",
	},
	"ASK-USER → resolved": {
		label: "Hold Resolved",
		symbol: "✓",
	},
	"REJECT-WITH-TRACE": {
		label: "Rejected with Trace",
		symbol: "✕",
	},
	"AUTO-FIX": { label: "Auto Fix", symbol: "⚡" },
};

export const EXPLORER_VIZ_LABELS: Record<string, string> = {
	ontology: "Ontology graph (Graph A)",
	dv: "Data Vault 2.0 topology",
	table: "Tabular view",
	kv: "Key / value view",
	checks: "Verification checks",
	events: "Event timeline",
	doc: "Document + span highlights",
	code: "Structured code & outline",
};

export const EXPLORER_EXTENDED_VIZ_LABELS: Record<string, string> = {
	...EXPLORER_VIZ_LABELS,
	ontology_coverage: "Ontology Grounding & Coverage",
	ontology_proposal: "Proposed Ontology Delta",
	ontology_diff: "Ontology Structural Diff",
};

export function getStatusTagStyle(tag: StatusTag | string): {
	bg: string;
	fg: string;
} {
	return EXPLORER_STATUS_TAGS[tag as StatusTag] ?? EXPLORER_STATUS_TAGS.PASS;
}

export function getStatusIndicator(tag: StatusTag | string): {
	label: string;
	symbol: string;
} {
	return (
		EXPLORER_STATUS_INDICATORS[tag as StatusTag] ?? {
			label: String(tag),
			symbol: "●",
		}
	);
}

export function getGlyphCssStyle(
	glyph: GlyphStyleSpec,
	size = 10,
): CSSProperties {
	return {
		width: `${size}px`,
		height: `${size}px`,
		flexShrink: 0,
		display: "inline-block",
		background: glyph.bg,
		border: glyph.border,
		borderRadius: glyph.radius,
		clipPath: glyph.clip,
	};
}
